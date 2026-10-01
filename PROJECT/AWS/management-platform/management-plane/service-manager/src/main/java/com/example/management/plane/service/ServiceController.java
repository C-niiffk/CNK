package com.example.management.plane.service;

import com.example.management.agent.*;
import com.example.management.common.contract.*;

import java.security.Principal;
import java.util.*;

import com.example.management.common.contract.AuditLog;import org.springframework.web.bind.annotation.*;
import org.springframework.security.access.prepost.PreAuthorize;

@RestController
@RequestMapping("/api/services")
public class ServiceController {
  private final GrdcClient grdc;
  private final AuditLog audit;

  public ServiceController(GrdcClient g, AuditLog a) {
    grdc = g;
    audit = a;
  }

  @GetMapping
  public Object services() throws Exception {
    return grdc.services();
  }

  @GetMapping("/{service}/instances")
  public Object instances(@PathVariable String service) throws Exception {
    return grdc.allInstances(service);
  }

  @GetMapping(value = "/{service}/config", produces = "text/plain")
  public String config(@PathVariable String service) throws Exception {
    check(service);
    String x = grdc.read(service + ".properties");
    return x == null ? "" : x;
  }

  @PutMapping("/{service}/config")
  @PreAuthorize("hasRole('ADMIN')")
  public Object config(@PathVariable String service, @RequestBody String body, Principal p) throws Exception {
    check(service);
    if (body.length() > 64000) throw new IllegalArgumentException("Config too large");
    if (!grdc.publish(service + ".properties", body, "properties")) throw new IllegalStateException("Publish failed");
    audit.record(p.getName(), "CONFIG_UPDATE", service);
    return Map.of("published", true);
  }

  private void check(String s) {
    if (!Set.of("app1", "app2").contains(s))
      throw new IllegalArgumentException("Only app1/app2 runtime config is editable here");
  }
}
