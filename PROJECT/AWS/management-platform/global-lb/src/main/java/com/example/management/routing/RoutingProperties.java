package com.example.management.routing;

import org.springframework.boot.context.properties.ConfigurationProperties;

import javax.naming.Binding;
import java.util.HashMap;
import java.util.Map;

@ConfigurationProperties("routing")
public class RoutingProperties {
  private String lockTable;
  private Map<String, Binding> bindings = new HashMap<>();

  public record Binding(String ruleArn, Map<String, String> targets, Map<String, String> probes) {}

  public String getLockTable() {
    return lockTable;
  }

  public void setLockTable(String s) {
    lockTable = s;
  }

  public Map<String, Binding> getBindings() {
    return bindings;
  }

  public void setBindings(Map<String, Binding> m) {
    bindings = m;
  }
}
