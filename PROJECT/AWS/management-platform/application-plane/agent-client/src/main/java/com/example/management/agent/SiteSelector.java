package com.example.management.agent;

import java.util.*;

import com.alibaba.nacos.api.naming.pojo.Instance;

public final class SiteSelector {
  private SiteSelector() {
  }

  public static List<String> candidates(String local, List<Instance> apps, List<Instance> gateways, Set<String> configuredSites) {
    var appSites = new HashSet<String>();
    var gatewaySites = new HashSet<String>();
    apps.stream().filter(SiteSelector::usable).map(i -> i.getMetadata().get("site")).filter(Objects::nonNull).forEach(appSites::add);
    gateways.stream().filter(SiteSelector::usable).map(i -> i.getMetadata().get("site")).filter(Objects::nonNull).forEach(gatewaySites::add);
    return configuredSites.stream().filter(s -> appSites.contains(s) && gatewaySites.contains(s))
      .sorted(Comparator.comparingInt((String s) -> s.equals(local) ? 0 : 1).thenComparing(s -> s)).toList();
  }

  static boolean usable(Instance i) {
    return i.isHealthy() && i.isEnabled() && i.getWeight() > 0;
  }
}
