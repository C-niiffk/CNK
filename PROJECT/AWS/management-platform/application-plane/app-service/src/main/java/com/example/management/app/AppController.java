package com.example.management.app;

import com.example.management.agent.*;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.security.oauth2.jwt.Jwt;

import java.util.Map;

@RestController
public class AppController {
  private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(AppController.class);
  private final AgentProperties p;
  private final RuntimeMessage config;
  private final CrossSystemClient client;

  public AppController(AgentProperties p, RuntimeMessage config, CrossSystemClient client) {
    this.p = p;
    this.config = config;
    this.client = client;
  }

  @GetMapping("/hello")
  public Object hello() {
    log.info("request method=GET path=/hello service={} site={}", p.service, p.site);
    return Map.of("service", p.service, "site", p.site, "instance", System.getenv().getOrDefault("HOSTNAME", "local"), "message", config.message());
  }

  @PostMapping("/echo")
  public Object echo(@RequestBody Map<String, Object> body) {
    log.info("request method=POST path=/echo service={} site={}", p.service, p.site);
    return Map.of("service", p.service, "site", p.site, "data", body);
  }

  @GetMapping("/call/{target}")
  public Object call(@PathVariable String target, @AuthenticationPrincipal Jwt jwt) throws Exception {
    return client.hello(target, jwt.getTokenValue());
  }
}
