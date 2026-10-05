package com.example.management.plane.core;

import com.example.management.agent.GrdcClient;
import com.example.management.common.contract.DocumentStore;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;
import org.springframework.scheduling.annotation.Scheduled;

@Component
public class ConfigReconciler {
  private final DocumentStore docs;
  private final GrdcClient grdc;
  private final ObjectMapper json;

  public ConfigReconciler(DocumentStore docs, GrdcClient grdc, ObjectMapper json) {
    this.docs = docs;
    this.grdc = grdc;
    this.json = json;
  }

  @Scheduled(fixedDelay = 10000, initialDelay = 10000)
  public void reconcile() {
    try {
      for (String raw : docs.list("config:")) {
        var c = json.readTree(raw);
        grdc.publish(c.get("id").asText(), c.get("body").asText(), c.get("type").asText());
      }
    } catch (Exception e) {
      org.slf4j.LoggerFactory.getLogger(getClass()).warn("Config reconciliation will retry", e);
    }
  }
}
