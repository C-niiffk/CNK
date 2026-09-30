MERGE INTO users u
  USING (SELECT 'nacos' username, '&NACOS_PASSWORD_HASH' password FROM dual) s
  ON (u.username = s.username)
  WHEN NOT MATCHED THEN
    INSERT (username, password, enabled) VALUES (s.username, s.password, 1);

MERGE INTO roles r
  USING (SELECT 'nacos' username, 'ROLE_ADMIN' role FROM dual) s
  ON (r.username = s.username AND r.role = s.role)
  WHEN NOT MATCHED THEN
    INSERT (username, role) VALUES (s.username, s.role);

COMMIT;
