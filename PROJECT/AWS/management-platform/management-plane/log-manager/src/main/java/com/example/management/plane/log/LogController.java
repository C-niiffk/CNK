package com.example.management.plane.log;

import com.example.management.common.*;
import com.example.management.common.contract.IntegrationClient;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Value;

import java.util.*;

@RestController
@RequestMapping("/api/logs")
public class LogController {
  private final IntegrationClient integrations;
  private final ObjectMapper json;
  private final String elastic;

  public LogController(IntegrationClient i, ObjectMapper j, @Value("${integration.elasticsearch:http://elasticsearch:9200}") String e) {
    integrations = i;
    json = j;
    elastic = e;
  }

  @GetMapping(produces = "application/json")
  public String logs(@RequestParam(defaultValue = "") String text, @RequestParam(defaultValue = "100") int size) throws Exception {
    if (text.length() > 500 || size < 1 || size > 200) throw new IllegalArgumentException("Invalid search size/text");
    Object q = text.isBlank() ? Map.of("match_all", Map.of()) : Map.of("match", Map.of("message", text));
    var body = Map.of("size", size, "sort", List.of(Map.of("@timestamp", "desc")), "query", Map.of("bool", Map.of("must", List.of(q), "filter", List.of(Map.of("range", Map.of("@timestamp", Map.of("gte", "now-24h")))))));
    var unique = new HashMap<String, com.fasterxml.jackson.databind.JsonNode>();
    int available = 0;
    for (String endpoint : elastic.split(","))
      try {
        var response = json.readTree(integrations.request(endpoint.trim() + "/platform-*/_search?ignore_unavailable=true", "POST", json.writeValueAsString(body)));
        available++;
        for (var hit : response.path("hits").path("hits")) unique.put(hit.path("_id").asText(), hit);
      } catch (Exception unavailable) { /* Query the surviving store when a site is unavailable. */ }
    if (available == 0)
      throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE, "No telemetry store available");
    var hits = unique.values().stream().sorted(Comparator.comparing((com.fasterxml.jackson.databind.JsonNode n) -> n.path("_source").path("@timestamp").asText()).reversed()).limit(size).toList();
    return json.writeValueAsString(Map.of("availableStores", available, "partial", available < elastic.split(",").length, "hits", Map.of("hits", hits)));

  }
}
