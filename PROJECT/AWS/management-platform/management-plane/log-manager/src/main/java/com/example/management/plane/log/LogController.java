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
    return integrations.request(elastic + "/platform-*/_search", "POST", json.writeValueAsString(body));
  }
}
