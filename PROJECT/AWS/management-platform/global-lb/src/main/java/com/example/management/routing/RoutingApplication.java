package com.example.management.routing;

import com.example.management.agent.AgentConfiguration;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
@Import(AgentConfiguration.class)
public class RoutingApplication {
  public static void main(String[] args) {
		SpringApplication.run(RoutingApplication.class, args);
  }
}
