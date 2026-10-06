-- Run separately as MP_GRDCA and MP_GRDCB after 02-nacos-schema.sql.
-- Paste a BCrypt hash of the EXISTING runtime NACOS_PASSWORD, never plaintext.
SET ECHO OFF
SET VERIFY OFF
SET SERVEROUTPUT ON
SET DEFINE ON
WHENEVER OSERROR EXIT FAILURE ROLLBACK
WHENEVER SQLERROR EXIT FAILURE ROLLBACK
ACCEPT NACOS_BCRYPT CHAR PROMPT 'Nacos BCrypt hash ($2a$12$...): ' HIDE
DECLARE
password_hash VARCHAR2(60) := '&NACOS_BCRYPT';
BEGIN
  IF SYS_CONTEXT('USERENV', 'SESSION_USER') NOT IN ('MP_GRDCA', 'MP_GRDCB')
     OR SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') <> SYS_CONTEXT('USERENV', 'SESSION_USER') THEN
    RAISE_APPLICATION_ERROR(-20002, 'Connect directly as MP_GRDCA or MP_GRDCB');
END IF;
  IF password_hash IS NULL OR LENGTH(password_hash) <> 60
     OR NOT REGEXP_LIKE(password_hash, '^\$2a\$12\$[./A-Za-z0-9]{53}$', 'c') THEN
    RAISE_APPLICATION_ERROR(-20004, 'Use a 60-character BCrypt 2a cost-12 hash');
END IF;
MERGE INTO users u
  USING (SELECT 'nacos' username, password_hash password FROM dual) s
  ON (u.username = s.username)
  WHEN NOT MATCHED THEN INSERT(username,password,enabled) VALUES(s.username,s.password,1);
MERGE INTO roles r
  USING (SELECT 'nacos' username, 'ROLE_ADMIN' role FROM dual) s
  ON (r.username=s.username AND r.role=s.role)
  WHEN NOT MATCHED THEN INSERT(username,role) VALUES(s.username,s.role);
COMMIT;
DBMS_OUTPUT.PUT_LINE('Admin seed completed; existing passwords were not changed.');
END;
/
UNDEFINE NACOS_BCRYPT
SELECT username, enabled FROM users WHERE username='nacos';
SELECT username, role FROM roles WHERE username='nacos';
