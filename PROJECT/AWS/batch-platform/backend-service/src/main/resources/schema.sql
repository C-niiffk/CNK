CREATE TABLE JOBS (
    id VARCHAR2(36) PRIMARY KEY,
    request_key VARCHAR2(100) NOT NULL UNIQUE,
    app VARCHAR2(10) NOT NULL,
    job_type VARCHAR2(3) NOT NULL,
    job_name VARCHAR2(64) NOT NULL,
    business_date VARCHAR2(10) NOT NULL,
    status VARCHAR2(10) NOT NULL,
    execution_claimed NUMBER(1) DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP) NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    result VARCHAR2(4000),
    CONSTRAINT ck_job_status CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','UNKNOWN')),
    CONSTRAINT ck_job_type CHECK (job_type IN ('SH','API')),
    CONSTRAINT ck_execution_claimed CHECK (execution_claimed IN (0,1))
);
CREATE INDEX JOBS_QUEUE_IDX ON JOBS(status,created_at);
GRANT SELECT ON JOBS TO BATCH_READER;
