package com.example.management.gateway;

import com.example.management.agent.*;
import com.example.management.common.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;
import jakarta.annotation.PostConstruct;

import java.util.*;
import java.util.concurrent.Executor;

import com.alibaba.nacos.api.config.listener.Listener;
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

  @PostConstruct
  void start() throws Exception {
    var raw = grdc.config().getConfigAndSignListener("gateway-policies.json", grdc.group(), 3000, new Listener() {
      public Executor getExecutor() {
        return null;
      }

      public void receiveConfigInfo(String s) {
        update(s);
      }
    });
    update(raw);
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
