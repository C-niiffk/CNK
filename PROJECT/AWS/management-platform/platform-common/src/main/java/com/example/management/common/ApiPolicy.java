package com.example.management.common;

import java.util.Set;

public record ApiPolicy(String service, boolean enabled, Set<String> roles, Set<String> allowedUsers,
                        Set<String> deniedUsers, Set<String> methods, int requestsPerSecond) {
  public ApiPolicy {
    if (!Set.of("app1", "app2").contains(service)) throw new IllegalArgumentException("Unknown service");
    roles = roles == null ? Set.of() : Set.copyOf(roles);
    allowedUsers = allowedUsers == null ? Set.of() : Set.copyOf(allowedUsers);
    deniedUsers = deniedUsers == null ? Set.of() : Set.copyOf(deniedUsers);
    methods = methods == null ? Set.of("GET") : Set.copyOf(methods);
    if (requestsPerSecond < 1 || requestsPerSecond > 10000) throw new IllegalArgumentException("Rate must be 1..10000");
    if (!Set.of("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD").containsAll(methods))
      throw new IllegalArgumentException("Invalid methods");
  }

  public boolean permits(String subject, Set<String> grantedRoles, String method) {
    return enabled && methods.contains(method) && !deniedUsers.contains(subject)
      && (allowedUsers.isEmpty() || allowedUsers.contains(subject)) && grantedRoles.stream().anyMatch(roles::contains);
  }
}
