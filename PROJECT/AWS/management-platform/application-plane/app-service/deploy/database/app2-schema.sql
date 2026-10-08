CREATE TABLE app2_job (
 id VARCHAR2(36) PRIMARY KEY,
 request_id VARCHAR2(100) NOT NULL UNIQUE,
 name VARCHAR2(200) NOT NULL,
 status VARCHAR2(20) NOT NULL,
 total_steps NUMBER(10) NOT NULL,
 checkpoint NUMBER(10) DEFAULT 0 NOT NULL,
 owner VARCHAR2(100),
 epoch NUMBER(19) DEFAULT 0 NOT NULL,
 lease_until TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 CONSTRAINT app2_job_status CHECK(status IN ('PENDING','RUNNING','SUCCEEDED')),
 CONSTRAINT app2_job_progress CHECK(checkpoint>=0 AND checkpoint<=total_steps)
);
CREATE INDEX app2_job_claim ON app2_job(status,lease_until);
