package com.example.management.plane.alarm;

import com.example.management.common.contract.*;
import com.example.management.agent.*;
import com.example.management.common.contract.IntegrationClient;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.web.bind.annotation.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.access.prepost.PreAuthorize;

import java.util.*;
import java.security.Principal;

@RestController
@RequestMapping("/api/alarms")
public class AlarmController {
  private final IntegrationClient integrations;
  private final String alerts;
  private final GrdcClient grdc;
  private final AuditLog audit;
  private final ObjectMapper json;

  public AlarmController(IntegrationClient i, @Value("${integration.alertmanager:http://alertmanager:9093}") String a, GrdcClient g, AuditLog u, ObjectMapper j) {
    integrations = i;
    alerts = a;
    grdc = g;
    audit = u;
    json = j;
  }

  @GetMapping(produces = "application/json")
  public String alerts() throws Exception {
    return integrations.request(alerts + "/api/v2/alerts", "GET", null);
  }

  @GetMapping(value = "/rules", produces = "application/json")
  public String rules() throws Exception {
    var r = grdc.read("alert-rules.json");
    return r == null ? "[]" : r;
  }

  public record Rule(String alert, String expr, int forSeconds, String severity) {
  }

  @PutMapping("/rules")
  @PreAuthorize("hasRole('ADMIN')")
  public Object rules(@RequestBody List<Rule> rules, Principal p) throws Exception {
    if (rules.size() > 100) throw new IllegalArgumentException("At most 100 rules");
    for (var r : rules)
      if (r.alert() == null || !r.alert().matches("[A-Za-z_][A-Za-z0-9_]{0,63}") || r.expr() == null || r.expr().length() > 2000 || r.forSeconds() < 0 || !Set.of("warning", "critical").contains(r.severity()))
        throw new IllegalArgumentException("Invalid alert rule");
    if (!grdc.publish("alert-rules.json", json.writeValueAsString(rules), "json"))
      throw new IllegalStateException("Publish failed");
    audit.record(p.getName(), "ALERT_RULES_UPDATE", "prometheus");
    return Map.of("published", true, "activation", "rule-sync validates with Prometheus before atomic reload");
  }
}
