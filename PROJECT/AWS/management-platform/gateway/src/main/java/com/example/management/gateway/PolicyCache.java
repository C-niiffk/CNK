package com.example.management.gateway;

import com.example.management.agent.*;
import com.example.management.common.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;


import java.util.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Component
public class PolicyCache {
  private static final Logger log = LoggerFactory.getLogger(PolicyCache.class);
  private volatile Map<String, ApiPolicy> policies = Map.of();
  private final GrdcClient grdc;
  private final ObjectMapper json;

  public PolicyCache(GrdcClient g, ObjectMapper j) {
    grdc = g;
    json = j;
  }

  @org.springframework.scheduling.annotation.Scheduled(fixedDelay = 5000, initialDelay = 0)
  void refresh() {
    try {
      update(grdc.read("gateway-policies.json"));
    } catch (Exception unavailable) {
      log.warn("Config unavailable; keeping last valid policy snapshot");
    }
  }

  void update(String raw) {
    if (raw == null || raw.isBlank()) {
      policies = Map.of();
      return;
    }
    try {
      var map = new HashMap<String, ApiPolicy>();
      for (var p : json.readValue(raw, ApiPolicy[].class))
        if (map.put(p.service(), p) != null) throw new IllegalArgumentException("Duplicate service");
      policies = Map.copyOf(map);
    } catch (Exception e) {
      log.error("Rejected invalid gateway policy; keeping last valid snapshot", e);
    }
  }

  public ApiPolicy get(String service) {
    return policies.get(service);
  }
}
