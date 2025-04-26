from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import FileResponse
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
import pandas as pd
import yaml
import os
import logging
import uuid
import schedule
import time
import threading
import traceback
from typing import Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import Base, SessionLocal, get_db, engine
from models import Job, User
from celery import Celery
import pickle
import threading as thread_module

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Report Generator Microservice")

# Celery configuration
celery_app = Celery(
    'report_generator',
    broker='redis://redis:6379/0',
    backend='redis://redis:6379/0'
)
celery_app.conf.update(
    task_serializer='pickle',
    accept_content=['pickle'],
    result_serializer='pickle',
    task_track_started=True,
    task_time_limit=3600  # 1 hour timeout per task
)

SECRET_KEY = os.getenv("SECRET_KEY") 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 300

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

CONFIG_DIR = "config"
INPUT_DIR = "inputs"
OUTPUT_DIR = "outputs"
TRANSFORM_CONFIG = os.path.join(CONFIG_DIR, "transform.yaml")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

class ScheduleConfig(BaseModel):
    cron_expression: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserCreate(BaseModel):
    username: str
    password: str

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

def authenticate_user(db: Session, username: str, password: str):
    user = get_user(db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user(db, username)
    if user is None or user.disabled:
        raise credentials_exception
    return user

def load_transformations():
    try:
        with open(TRANSFORM_CONFIG, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading transformation config: {e}")
        raise HTTPException(status_code=500, detail="Failed to load transformation config")

def apply_transformations(input_df: pd.DataFrame, ref_df: pd.DataFrame, transformations: Dict) -> pd.DataFrame:
    output_df = pd.DataFrame()

    context = {
        **{col: input_df[col] for col in input_df.columns},
        **{col: ref_df[col] for col in ref_df.columns},
        'max': max,
        'min': min,
        'len': len,
        'float': float,
        'pd': pd
    }

    for out_field, rule in transformations.items():
        try:
            if isinstance(rule, str):
                output_df[out_field] = eval(rule, {}, context)
            else:
                output_df[out_field] = rule(input_df, ref_df)
        except Exception as e:
            logger.error(f"Error applying transformation for {out_field}: {e}")
            logger.error(f"Exception details: {str(e)}")
            logger.error(f"Transformation rule: {rule}")
            raise HTTPException(status_code=500, detail=f"Transformation error for {out_field}: {str(e)}")

    return output_df

@celery_app.task
def process_chunk(chunk_data, ref_data, transformations, chunk_index, output_file):
    try:
        logger.info(f"Processing chunk {chunk_index} with {len(chunk_data)} rows")
        chunk_df = pd.DataFrame(chunk_data)
        ref_df = pd.DataFrame(ref_data)
        output_chunk = apply_transformations(chunk_df, ref_df, transformations)
        if output_chunk is None:
            raise ValueError("Transformation returned None")
        
        # Write to temporary file to avoid conflicts
        temp_file = f"{output_file}.chunk_{chunk_index}"
        output_chunk.to_csv(temp_file, index=False)
        logger.info(f"Completed chunk {chunk_index}")
        return temp_file
    except Exception as e:
        logger.error(f"Error in chunk {chunk_index}: {e}")
        logger.error(traceback.format_exc())
        raise

def process_report(input_file: str, ref_file: str, output_file: str):
    try:
        start_time = time.time()
        logger.info(f"Starting report generation for {input_file}")
        
        if not os.path.exists(input_file):
            raise HTTPException(status_code=404, detail=f"Input file not found: {input_file}")
        if not os.path.exists(ref_file):
            raise HTTPException(status_code=404, detail=f"Reference file not found: {ref_file}")
        
        ref_df = pd.read_csv(ref_file)
        
        required_input_cols = ['field1', 'field2', 'field3', 'field4', 'field5', 'refkey1', 'refkey2']
        required_ref_cols = ['refkey1', 'refdata1', 'refkey2', 'refdata2', 'refdata3', 'refdata4']
        missing_input_cols = [col for col in required_input_cols if col not in pd.read_csv(input_file, nrows=1).columns]
        missing_ref_cols = [col for col in required_ref_cols if col not in ref_df.columns]
        if missing_input_cols:
            raise HTTPException(status_code=400, detail=f"Missing input columns: {missing_input_cols}")
        if missing_ref_cols:
            raise HTTPException(status_code=400, detail=f"Missing reference columns: {missing_ref_cols}")
        
        transformations = load_transformations()
        chunk_count = 0
        tasks = []
        
        # Serialize reference dataframe
        ref_data = ref_df.to_dict('records')
        
        for chunk in pd.read_csv(input_file, chunksize=1000):
            chunk_count += 1
            chunk_data = chunk.to_dict('records')
            task = process_chunk.delay(chunk_data, ref_data, transformations, chunk_count, output_file)
            tasks.append(task)
        
        # Wait for all tasks to complete
        temp_files = []
        for task in tasks:
            result = task.get()  # Blocks until task completes
            temp_files.append(result)
        
        # Combine temporary files into final output
        first_chunk = True
        for temp_file in temp_files:
            temp_df = pd.read_csv(temp_file)
            temp_df.to_csv(output_file, index=False, mode='w' if first_chunk else 'a', header=first_chunk)
            first_chunk = False
            os.remove(temp_file)  # Clean up
        
        logger.info(f"Report generated in {time.time() - start_time:.2f} seconds, processed {chunk_count} chunks")
        return output_file
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Report generation failed: {str(e)}")

@app.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/users", response_model=Token)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = get_user(db, user.username)
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    hashed_password = pwd_context.hash(user.password)
    new_user = User(username=user.username, hashed_password=hashed_password, disabled=False)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/upload/input", dependencies=[Depends(get_current_user)])
async def upload_input(file: UploadFile = File(...)):
    file_path = os.path.join(INPUT_DIR, f"input_{uuid.uuid4()}.csv")
    with open(file_path, 'wb') as f:
        f.write(await file.read())
    logger.info(f"Input file uploaded: {file_path}")
    return {"filename": os.path.basename(file_path)}

@app.post("/upload/reference", dependencies=[Depends(get_current_user)])
async def upload_reference(file: UploadFile = File(...)):
    file_path = os.path.join(INPUT_DIR, f"ref_{uuid.uuid4()}.csv")
    with open(file_path, 'wb') as f:
        f.write(await file.read())
    logger.info(f"Reference file uploaded: {file_path}")
    return {"filename": os.path.basename(file_path)}

@app.post("/generate-report", dependencies=[Depends(get_current_user)])
async def generate_report(input_filename: str, ref_filename: str, db: Session = Depends(get_db)):
    input_path = os.path.join(INPUT_DIR, input_filename)
    ref_path = os.path.join(INPUT_DIR, ref_filename)
    output_filename = f"report_{uuid.uuid4()}.csv"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    if not os.path.exists(input_path) or not os.path.exists(ref_path):
        raise HTTPException(status_code=404, detail="Input or reference file not found")
    
    job_id = str(uuid.uuid4())
    new_job = Job(id=job_id, status="processing", filename=output_filename)
    db.add(new_job)
    db.commit()
    
    threading.Thread(
        target=process_report_with_error_handling,
        args=(input_path, ref_path, output_path, job_id),
        daemon=True
    ).start()
    
    return {"job_id": job_id, "status": "processing", "report_filename": output_filename}

def process_report_with_error_handling(input_path, ref_path, output_path, job_id):
    try:
        process_report(input_path, ref_path, output_path)
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "completed"
                db.commit()
        finally:
            db.close()
    except Exception as e:
        error_message = f"{str(e)}\n{traceback.format_exc()}"
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "failed"
                job.error = error_message
                db.commit()
        finally:
            db.close()
        logger.error(f"Background processing error for job {job_id}: {error_message}")

@app.get("/job-status/{job_id}", dependencies=[Depends(get_current_user)])
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": job.status,
        "report_filename": job.filename,
        "error": job.error if job.status == "failed" else None
    }

@app.get("/download/{filename}", dependencies=[Depends(get_current_user)])
async def download_report(filename: str):
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(file_path, filename=filename)

@app.post("/configure-transformations", dependencies=[Depends(get_current_user)])
async def configure_transformations(config: Dict):
    try:
        with open(TRANSFORM_CONFIG, 'w') as f:
            yaml.dump(config, f)
        logger.info("Transformation rules updated")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error updating transformation config: {e}")
        raise HTTPException(status_code=500, detail="Failed to update transformation config")

@app.post("/schedule", dependencies=[Depends(get_current_user)])
async def set_schedule(config: ScheduleConfig):
    try:
        schedule.every().day.at(config.cron_expression).do(process_report, 
            input_file=f"{INPUT_DIR}/input.csv", 
            ref_file=f"{INPUT_DIR}/reference.csv",
            output_file=f"{OUTPUT_DIR}/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        logger.info(f"Schedule set with cron: {config.cron_expression}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error setting schedule: {e}")
        raise HTTPException(status_code=500, detail="Failed to set schedule")

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

@app.on_event("startup")
async def startup_event():
    Base.metadata.create_all(bind=engine)
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("Scheduler started")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)