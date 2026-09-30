package com.example.management.routing;

import java.util.*;

public final class WeightDecision {
  private WeightDecision() {}

  public static Map<String, Integer> weights(Map<String, Boolean> healthy) {
    var result = new TreeMap<String, Integer>();
    healthy.forEach((k, v) -> result.put(k, v ? 100 : 0));
    return result;
  }

  public static boolean unavailable(Map<String, Integer> weights) {
    return weights.values().stream().noneMatch(w -> w > 0);
  }
}
