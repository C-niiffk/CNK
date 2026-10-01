CREATE TABLE platform_user
(
  username      VARCHAR2(64) PRIMARY KEY,
  password_hash VARCHAR2(100) NOT NULL,
  roles         VARCHAR2(100) NOT NULL,
  enabled       NUMBER(1) DEFAULT 1 NOT NULL
);
CREATE TABLE platform_document
(
  id        VARCHAR2(100) PRIMARY KEY,
  json_body CLOB NOT NULL,
  revision  NUMBER(19) DEFAULT 0 NOT NULL
);
CREATE TABLE platform_audit
(
  id          VARCHAR2(36) PRIMARY KEY,
  actor       VARCHAR2(64) NOT NULL,
  action      VARCHAR2(100) NOT NULL,
  target      VARCHAR2(200) NOT NULL,
  occurred_at VARCHAR2(40) NOT NULL
);
CREATE INDEX audit_time_idx ON platform_audit (occurred_at);
