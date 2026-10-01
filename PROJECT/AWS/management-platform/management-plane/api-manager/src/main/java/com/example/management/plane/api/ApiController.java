package com.example.management.plane.api;

import com.example.management.common.*;
import com.example.management.common.contract.*;

import java.security.Principal;

import org.springframework.web.bind.annotation.*;
import org.springframework.security.access.prepost.PreAuthorize;

@RestController
@RequestMapping("/api/apis")
public class ApiController {
  private final PolicyService policies;
  private final AuditLog audits;

  public ApiController(PolicyService p, AuditLog a) {
    policies = p;
    audits = a;
  }

  @GetMapping
  public Object list() throws Exception {
    return policies.list();
  }

  @PutMapping
  @PreAuthorize("hasRole('ADMIN')")
  public Object save(@RequestBody ApiPolicy p, Principal u) throws Exception {
    policies.save(p, u.getName());
    return p;
  }

  @PostMapping("/publish")
  @PreAuthorize("hasRole('ADMIN')")
  public Object publish(Principal u) throws Exception {
    var r = policies.publish();
    audits.record(u.getName(), "POLICY_PUBLISH", "gateway");
    return r;
  }
}
