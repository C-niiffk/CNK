package com.example.management.plane.core;

import jakarta.persistence.*;

@Entity
@Table(name = "platform_audit")
public class AuditEntry {
  @Id
  public String id = java.util.UUID.randomUUID().toString();
  @Column(nullable = false)
  public String actor;
  @Column(nullable = false)
  public String action;
  @Column(nullable = false)
  public String target;
  @Column(name = "occurred_at", nullable = false)
  public String occurredAt = java.time.Instant.now().toString();

  protected AuditEntry() {
  }

  public AuditEntry(String a, String b, String c) {
    actor = a;
    action = b;
    target = c;
  }
}
