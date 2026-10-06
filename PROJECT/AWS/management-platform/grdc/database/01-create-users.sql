SET ECHO OFF
SET VERIFY OFF
SET SERVEROUTPUT ON
SET DEFINE ON
WHENEVER OSERROR EXIT FAILURE ROLLBACK
WHENEVER SQLERROR EXIT FAILURE ROLLBACK
ACCEPT GRDC_A_PASSWORD CHAR PROMPT 'MP_GRDCA password from runtime Secret (24-64 alphanumeric): ' HIDE
ACCEPT GRDC_B_PASSWORD CHAR PROMPT 'MP_GRDCB password from runtime Secret (24-64 alphanumeric): ' HIDE
DECLARE
PROCEDURE validate_password(p_password VARCHAR2) IS
BEGIN
    IF p_password IS NULL OR NOT REGEXP_LIKE(p_password, '^[A-Za-z0-9]{24,64}$', 'c') THEN
      RAISE_APPLICATION_ERROR(-20001, 'Schema password must contain 24-64 alphanumeric characters');
END IF;
END;
  PROCEDURE ensure_user(p_user VARCHAR2, p_password VARCHAR2) IS
    n NUMBER;
BEGIN
SELECT COUNT(*) INTO n FROM all_users WHERE username = p_user;
IF n = 0 THEN
      EXECUTE IMMEDIATE 'CREATE USER ' || p_user || ' IDENTIFIED BY "' || p_password || '" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS';
      DBMS_OUTPUT.PUT_LINE(p_user || ': created');
ELSE
      DBMS_OUTPUT.PUT_LINE(p_user || ': already exists; password unchanged');
END IF;
EXECUTE IMMEDIATE 'GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE TRIGGER TO ' || p_user;
END;
BEGIN
  validate_password('&GRDC_A_PASSWORD');
  validate_password('&GRDC_B_PASSWORD');
  ensure_user('MP_GRDCA', '&GRDC_A_PASSWORD');
  ensure_user('MP_GRDCB', '&GRDC_B_PASSWORD');
END;
/
UNDEFINE GRDC_A_PASSWORD
UNDEFINE GRDC_B_PASSWORD
PROMPT Schema account step completed. Existing passwords were not changed.
