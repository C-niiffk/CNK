package com.example.management.plane;

import com.example.management.plane.user.*;
import org.springframework.stereotype.Component;
import org.springframework.boot.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

@Component
public class AdminInitializer implements ApplicationRunner {
  private final Users users;
  private final String adminPassword;

  public AdminInitializer(Users u, @Value("${platform.admin-password}") String p) {
    users = u;
    adminPassword = p;
  }

  public void run(ApplicationArguments args) {
    if (adminPassword.length() < 12)
      throw new IllegalArgumentException("ADMIN_PASSWORD must be at least 12 characters");
    if (users.findById("admin").isEmpty()) try {
      users.save(new PlatformUser("admin", new BCryptPasswordEncoder(12).encode(adminPassword), "ADMIN,API,VIEWER"));
    } catch (org.springframework.dao.DataIntegrityViolationException race) {
      if (users.findById("admin").isEmpty()) throw race;
    }
  }
}
