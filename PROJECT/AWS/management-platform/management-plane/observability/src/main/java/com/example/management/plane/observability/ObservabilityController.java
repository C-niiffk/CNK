package com.example.management.plane.observability;

import com.example.management.common.contract.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Value;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

@RestController
@RequestMapping("/api/observability")
public class ObservabilityController {
  private final IntegrationClient integrations;
  private final String prom;

  public ObservabilityController(IntegrationClient i, @Value("${integration.prometheus:http://prometheus:9090}") String p) {
    integrations = i;
    prom = p;
  }

  @GetMapping(value = "/query", produces = "application/json")
  public String query(@RequestParam(defaultValue = "up") String query) throws Exception {
    if (query.length() > 2000) throw new IllegalArgumentException("Query too long");
    return integrations.request(prom + "/api/v1/query?query=" + URLEncoder.encode(query, StandardCharsets.UTF_8), "GET", null);
  }
}
