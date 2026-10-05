package com.example.management.app.state;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.springframework.context.annotation.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.actuate.health.*;
import org.springframework.scheduling.annotation.EnableScheduling;

@Configuration
@Profile("app2")
@EnableScheduling
public class JobConfiguration {
  @Bean(destroyMethod = "close")
  HikariDataSource app2DataSource(
    @Value("${APP2_DB_URL}") String url, @Value("${APP2_DB_PASSWORD}") String password) {
    HikariConfig c = new HikariConfig();
    c.setJdbcUrl(url);
    c.setUsername("MP_APP2");
    c.setPassword(password);
    c.setMaximumPoolSize(4);
    c.setMinimumIdle(0);
    c.setConnectionTimeout(5000);
    c.setValidationTimeout(2000);
    c.setConnectionInitSql("ALTER SESSION SET TIME_ZONE = '+00:00'");
    c.setInitializationFailTimeout(-1);
    c.addDataSourceProperty("oracle.net.CONNECT_TIMEOUT", "5000");
    c.addDataSourceProperty("oracle.jdbc.ReadTimeout", "5000");
    c.addDataSourceProperty("oracle.net.encryption_client", "REQUIRED");
    c.addDataSourceProperty("oracle.net.crypto_checksum_client", "REQUIRED");
    return new HikariDataSource(c);
  }

  @Bean
  JobStore jobStore(HikariDataSource dataSource) {
    return new JobStore(dataSource);
  }

  @Bean
  HealthIndicator app2Database(HikariDataSource dataSource) {
    return () -> {
      try (var c = dataSource.getConnection(); var p = c.prepareStatement("SELECT 1 FROM app2_job WHERE 1=0")) {
        p.setQueryTimeout(3);
        p.executeQuery().close();
        return Health.up().build();
      } catch (Exception e) {
        return Health.down().withDetail("database", "unavailable").build();
      }
    };
  }
}
