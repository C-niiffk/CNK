package com.example.batch;

import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.util.Properties;

/** DB Initializer **/

final class DBInitializer {
  private DBInitializer() {}

  static void initialize() throws SQLException {
    String owner = VerifyProcess.required("OWNER_PASSWORD");
    String reader = VerifyProcess.required("READER_PASSWORD");
    if (!owner.matches("[A-Za-z0-9]{32,64}") || !reader.matches("[A-Za-z0-9]{32,64}")) {
      throw new IllegalArgumentException("DB passwords invalid");
    }

    try (Connection connection = open(VerifyProcess.required("DB_USER"), VerifyProcess.required("DB_PASSWORD"));
         var statement = connection.createStatement()) {
      try (var users = statement.executeQuery(
        "SELECT COUNT(*) FROM ALL_USERS WHERE USERNAME IN ('BATCH_OWNER','BATCH_READER')")) {
        users.next();
        if (users.getInt(1) != 0) {
          throw new IllegalStateException(
            "Schema users already exist");
        }
      }

      statement.execute("CREATE USER BATCH_OWNER IDENTIFIED BY \"" + owner
        + "\" DEFAULT TABLESPACE USERS QUOTA 100M ON USERS");
      statement.execute("GRANT CREATE SESSION, CREATE TABLE TO BATCH_OWNER");
      statement.execute("CREATE USER BATCH_READER IDENTIFIED BY \"" + reader + "\"");
      statement.execute("GRANT CREATE SESSION TO BATCH_READER");
    }
    try (Connection connection = open("BATCH_OWNER", owner)) {
      ScriptUtils.executeSqlScript(connection, new ClassPathResource("schema.sql"));
    }
    try (Connection connection = open(VerifyProcess.required("DB_USER"), VerifyProcess.required("DB_PASSWORD"));
         var statement = connection.createStatement()) {
      statement.execute("REVOKE CREATE TABLE FROM BATCH_OWNER");
    }
    System.out.println("SCHEMA_INITIALIZED version=1");
  }

  private static Connection open(String username, String password) throws SQLException {
    Properties properties = new Properties();
    properties.setProperty("user", username);
    properties.setProperty("password", password);
    properties.setProperty("oracle.net.CONNECT_TIMEOUT", "5000");
    properties.setProperty("oracle.jdbc.ReadTimeout", "15000");
    properties.setProperty("oracle.net.encryption_client", "REQUIRED");
    properties.setProperty("oracle.net.encryption_types_client", "(AES256)");
    properties.setProperty("oracle.net.crypto_checksum_client", "REQUIRED");
    properties.setProperty("oracle.net.crypto_checksum_types_client", "(SHA256)");
    Connection connection = DriverManager.getConnection(VerifyProcess.required("DB_URL"), properties);
    try (var statement = connection.createStatement()) {
      statement.execute("ALTER SESSION SET TIME_ZONE='+00:00'");
    } catch (SQLException error) {
      connection.close();
      throw error;
    }
    return connection;
  }

}
