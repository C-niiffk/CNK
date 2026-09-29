package com.example.management.gateway;

import com.example.management.agent.AgentConfiguration;
import com.example.management.common.Tokens;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;

@SpringBootApplication
@Import({AgentConfiguration.class, Tokens.class})
public class GatewayApplication {
  public static void main(String[] args) {
    SpringApplication.run(GatewayApplication.class, args);
  }
}
