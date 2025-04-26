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
from typing import Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import Base, SessionLocal, get_db, engine
from models import User

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Report Generator Microservice")

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

# Load transformation rules
def load_transformations():
    try:
        with open(TRANSFORM_CONFIG, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Error loading transformation config: {e}")
        raise HTTPException(status_code=500, detail="Failed to load transformation config")

# Apply transformations
def apply_transformations(input_df: pd.DataFrame, ref_df: pd.DataFrame, transformations: Dict) -> pd.DataFrame:
    output_df = pd.DataFrame()

    # Create a context dictionary dynamically with all columns
    input_context = {col: input_df[col] for col in input_df.columns}
    ref_context = {col: ref_df[col] for col in ref_df.columns}

    # Merge both into a single context for eval
    context = {**input_context, **ref_context, 'max': max, 'min': min, 'len': len}

    for out_field, rule in transformations.items():
        try:
            if isinstance(rule, str):
                output_df[out_field] = eval(rule, {}, context)
            else:
                # If rule is a function
                output_df[out_field] = rule(input_df, ref_df)
        except Exception as e:
            logger.error(f"Error applying transformation for {out_field}: {e}")
            raise HTTPException(status_code=500, detail=f"Transformation error for {out_field}")

    return output_df


def process_report(input_file: str, ref_file: str, output_file: str):
    try:
        start_time = time.time()
        logger.info("Starting report generation")
        
        input_df = pd.read_csv(input_file, chunksize=10000)
        ref_df = pd.read_csv(ref_file)
        
        merged_df = pd.DataFrame()
        for chunk in input_df:
            chunk_merged = chunk.merge(ref_df, on=['refkey1', 'refkey2'], how='left')
            merged_df = pd.concat([merged_df, chunk_merged])
        
        transformations = load_transformations()
        
        output_df = apply_transformations(merged_df, ref_df, transformations)
        
        output_df.to_csv(output_file, index=False)
        
        logger.info(f"Report generated in {time.time() - start_time:.2f} seconds")
        return output_file
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        raise HTTPException(status_code=500, detail="Report generation failed")

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
async def generate_report(input_filename: str, ref_filename: str):
    input_path = os.path.join(INPUT_DIR, input_filename)
    ref_path = os.path.join(INPUT_DIR, ref_filename)
    output_filename = f"report_{uuid.uuid4()}.csv"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    if not os.path.exists(input_path) or not os.path.exists(ref_path):
        raise HTTPException(status_code=404, detail="Input or reference file not found")
    
    output_file = process_report(input_path, ref_path, output_path)
    return {"report_filename": os.path.basename(output_file)}

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