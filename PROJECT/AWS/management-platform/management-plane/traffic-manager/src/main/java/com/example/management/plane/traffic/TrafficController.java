package com.example.management.plane.traffic;

import com.example.management.common.contract.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Value;

import java.util.Map;

@RestController
@RequestMapping("/api/traffic")
public class TrafficController {
  private final IntegrationClient integrations;
  private final ObjectMapper json;
  private final String oap;

  public TrafficController(IntegrationClient i, ObjectMapper j, @Value("${integration.skywalking:http://oap:12800}") String o) {
    integrations = i;
    json = j;
    oap = o;
  }

  public record Query(String query, Map<String, Object> variables) {
  }

  @PostMapping(value = "/graphql", produces = "application/json")
  public String query(@RequestBody Query q) throws Exception {
    if (q.query() == null || q.query().length() > 12000 || q.query().matches("(?s).*\\bmutation\\b.*"))
      throw new IllegalArgumentException("Read-only GraphQL queries only");
    return integrations.request(oap + "/graphql", "POST", json.writeValueAsString(q));
  }
}
