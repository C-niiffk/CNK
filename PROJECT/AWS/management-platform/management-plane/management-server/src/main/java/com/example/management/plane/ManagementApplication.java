package com.example.management.plane;

import org.springframework.boot.*;
import org.springframework.boot.autoconfigure.*;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.context.annotation.Import;
import com.example.management.common.Tokens;
import com.example.management.agent.AgentConfiguration;

@SpringBootApplication
@EntityScan("com.example.management.plane")
@EnableJpaRepositories("com.example.management.plane")
@Import({Tokens.class, AgentConfiguration.class})
public class ManagementApplication {
  public static void main(String[] args) {
    SpringApplication.run(ManagementApplication.class, args);
  }
}
