# Report Generator Microservice

The **Report Generator Microservice** is a FastAPI-based application designed to generate reports from input CSV files by applying transformation rules defined in a YAML configuration. It supports asynchronous report generation using Celery for parallel processing, JWT-based authentication, and a PostgreSQL database for job tracking. The service is containerized with Docker Compose, using Redis as the Celery broker and PostgreSQL for persistent storage. Swagger-UI is integrated for interactive API documentation.

This microservice is ideal for processing large datasets (up to ~1GB) with complex transformations, offering a scalable and secure solution for report generation tasks.

## Features

- **Asynchronous Report Generation**: Processes CSV files in parallel using Celery workers, optimized for large datasets.
- **JWT Authentication**: Secure endpoints with user authentication and token-based access.
- **Swagger-UI Documentation**: Interactive API documentation at `/docs` for easy testing and exploration.
- **Transformation Rules**: Flexible YAML-based rules for data transformation (e.g., combining fields, applying mathematical operations).
- **Job Tracking**: Stores job status and errors in a PostgreSQL database.
- **Scheduling**: Supports daily report generation via cron-like scheduling.
- **Dockerized Deployment**: Runs in containers with FastAPI, Celery, Redis, and PostgreSQL.

## Prerequisites

- **Docker** and **Docker Compose**: For containerized deployment.
- **Python 3.8+**: For local development (optional, if not using Docker).
- **Git**: To clone the repository.
- **curl** or **Postman**: For testing API endpoints.
- A modern web browser to access Swagger-UI.

## Project Structure

```
Report-Generator-Microservice/
├── config/
│   └── transform.yaml       # Transformation rules
├── inputs/                 # Uploaded input and reference CSVs
├── outputs/                # Generated reports
├── database.py             # SQLAlchemy database setup
├── main.py                 # FastAPI application
├── models.py               # SQLAlchemy models (Job, User)
├── requirements.txt        # Python dependencies
├── docker-compose.yml      # Docker Compose configuration
└── README.md               # This file
```

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/Dushyant98474839/Report-Generator-Microservice.git
cd Report-Generator-Microservice
```

### 2. Configure Environment

Create a secure `SECRET_KEY` for JWT authentication:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```


### 3. Start Docker Services

Build and start the containers:

```bash
docker-compose up --build -d
```

Verify services are running:

```bash
docker ps
```

Expected containers:
- `rp2_app_1`: FastAPI application (port 8000)
- `rp2_celery_1`: Celery worker
- `rp2_redis_1`: Redis broker (port 6379)
- `rp2_postgres_1`: PostgreSQL database (port 5432)

### 4. Verify Swagger-UI

Open a browser and navigate to:

```
http://localhost:8000/docs
```

This displays the interactive API documentation.

## Usage

### 1. Create a User

Register a user to obtain a JWT token:

```bash
curl -X POST "http://localhost:8000/users" \
     -H "Content-Type: application/json" \
     -d '{"username":"testuser","password":"testpassword"}'
```

Response:
```json
{
  "access_token": "<your-jwt-token>",
  "token_type": "bearer"
}
```

### 2. Log In

Authenticate to get a JWT token:

```bash
curl -X POST "http://localhost:8000/token" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "username=testuser&password=testpassword"
```

Save the `access_token` for authenticated requests.

### 3. Upload Input and Reference Files

Prepare CSV files with the required structure:

**Input CSV** (`input.csv`):
```
field1,field2,field3,field4,field5,refkey1,refkey2
a,c,1,x,10.0,k1,r1
b,d,2,y,20.0,k2,r2
```

**Reference CSV** (`reference.csv`):
```
refkey1,refdata1,refkey2,refdata2,refdata3,refdata4
k1,rd1,r1,rd3,rd5,5.0
k2,rd2,r2,rd4,rd6,15.0
```

Upload files:

```bash
curl -X POST "http://localhost:8000/upload/input" \
     -H "Authorization: Bearer <your-jwt-token>" \
     -F "file=@./inputs/input.csv"
curl -X POST "http://localhost:8000/upload/reference" \
     -H "Authorization: Bearer <your-jwt-token>" \
     -F "file=@./inputs/reference.csv"
```

Note the returned filenames (e.g., `input_<uuid>.csv`, `ref_<uuid>.csv`).

### 4. Generate a Report

Start a report generation job:

```bash
curl -X POST "http://localhost:8000/generate-report" \
     -H "Authorization: Bearer <your-jwt-token>" \
     -H "Content-Type: application/json" \
     -d '{"input_filename": "<input-filename>", "ref_filename": "<ref-filename>"}'
```

Response:
```json
{
  "job_id": "<job-id>",
  "status": "processing",
  "report_filename": "report_<uuid>.csv"
}
```

### 5. Check Job Status

Monitor the job:

```bash
curl -X GET "http://localhost:8000/job-status/<job-id>" \
     -H "Authorization: Bearer <your-jwt-token>"
```

Response (example):
```json
{
  "status": "completed",
  "report_filename": "report_<uuid>.csv",
  "error": null
}
```

### 6. Download Report

When status is `"completed"`, download the report:

```bash
curl -X GET "http://localhost:8000/download/<report-filename>" \
     -H "Authorization: Bearer <your-jwt-token>" \
     -o ./outputs/<report-filename>
```

### 7. Use Swagger-UI

- Visit `http://localhost:8000/docs`.
- Click “Authorize” and enter `Bearer <your-jwt-token>`.
- Test endpoints like `/generate-report` or `/job-status` interactively.

## API Documentation

The API is documented via Swagger-UI at `http://localhost:8000/docs`. Key endpoints include:

- **POST /token**: Authenticate and get a JWT token (Authentication).
- **POST /users**: Create a new user (Authentication).
- **POST /upload/input**: Upload input CSV (File Upload).
- **POST /upload/reference**: Upload reference CSV (File Upload).
- **POST /generate-report**: Start report generation (Report Generation).
- **GET /job-status/{job_id}**: Check job status (Report Generation).
- **GET /download/{filename}**: Download report (Report Generation).
- **POST /configure-transformations**: Update transformation rules (Configuration).
- **POST /schedule**: Schedule daily reports (Configuration).

Each endpoint includes detailed descriptions and response schemas in Swagger-UI.

## Transformation Rules

Transformation rules are defined in `config/transform.yaml`. Example:

```yaml
outfield1: "field1 + field2"
outfield2: "refdata1"
outfield3: "refdata2 + refdata3"
outfield4: "pd.to_numeric(field3) * pd.Series([max(x, y) for x, y in zip(field5, refdata4)])"
outfield5: "pd.Series([max(x, y) for x, y in zip(field5, refdata4)])"
```

Update rules via:

```bash
curl -X POST "http://localhost:8000/configure-transformations" \
     -H "Authorization: Bearer <your-jwt-token>" \
     -H "Content-Type: application/json" \
     -d '{"outfield1": "field1 + field2", ...}'
```

## Scheduling Reports

Schedule daily reports:

```bash
curl -X POST "http://localhost:8000/schedule" \
     -H "Authorization: Bearer <your-jwt-token>" \
     -H "Content-Type: application/json" \
     -d '{"cron_expression": "14:30"}'
```

This runs a report daily at 14:30 using `inputs/input.csv` and `inputs/reference.csv`.

## Troubleshooting

### Job Stuck in "processing"
- **Check Logs**:
  ```bash
  docker-compose logs celery | grep "Starting chunk\|Completed chunk\|error"
  docker-compose logs app | grep "Enqueuing chunk\|Task completed\|error"
  ```
- **Reduce Resource Usage**:
  Edit `main.py` to set `chunksize=500` (line ~208):
  ```python
  for chunk in pd.read_csv(input_file, chunksize=500):
  ```
  Or reduce Celery concurrency in `docker-compose.yml`:
  ```yaml
  celery:
    command: celery -A main.celery_app worker --loglevel=info --concurrency=1
  ```
  Rebuild:
  ```bash
  docker-compose up --build -d
  ```

### Docker Network Errors
- If `docker-compose down` fails with `has active endpoints`:
  ```bash
  docker rm -f $(docker ps -a -q)
  docker network rm $(docker network ls -q)
  docker system prune -f
  docker volume prune -f
  ```

### Authentication Issues
- Ensure a valid JWT token:
  ```bash
  curl -X POST "http://localhost:8000/token" \
       -H "Content-Type: application/x-www-form-urlencoded" \
       -d "username=testuser&password=testpassword"
  ```

### Disk Space Issues
- Check disk space:
  ```bash
  df -h
  ```
- Clean up temporary files:
  ```bash
  rm -f ./outputs/*.chunk_*
  ```

## Performance Notes

- **Large Files**: Optimized for CSVs up to 1GB with `chunksize=1000` and 2 Celery workers. For larger files, reduce `chunksize` or concurrency.
- **Memory**: Containers are limited to 8GB each. Monitor with:
  ```bash
  docker stats
  ```
- **Logging**: Detailed logs in `main.py` help diagnose issues with chunk processing.

## Contributing

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/YourFeature`).
3. Commit changes (`git commit -m "Add YourFeature"`).
4. Push to the branch (`git push origin feature/YourFeature`).
5. Open a Pull Request.

Please include tests and update documentation for new features.
