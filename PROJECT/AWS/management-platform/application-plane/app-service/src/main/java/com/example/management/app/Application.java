package com.example.management.app;

import com.example.management.agent.AgentConfiguration;
import org.apache.el.parser.Token;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;

@SpringBootApplication
@Import({AgentConfiguration.class, Token.class})
public class Application {
  public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
