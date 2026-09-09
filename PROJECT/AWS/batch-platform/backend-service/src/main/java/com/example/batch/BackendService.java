package com.example.batch;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.security.servlet.UserDetailsServiceAutoConfiguration;
import org.springframework.scheduling.annotation.EnableScheduling;

@EnableScheduling
@SpringBootApplication(exclude = UserDetailsServiceAutoConfiguration.class)
public class BackendService {
  public static void main(String[] args) throws Exception {

    if (args.length == 1 && "init-db".equals(args[0])) {
      DBInitializer.initialize();
      return;
    }
    SpringApplication.run(BackendService.class, args);
  }
}
