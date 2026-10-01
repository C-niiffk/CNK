package com.example.management.plane.core;

import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;

public interface Audits extends JpaRepository<AuditEntry, String> {
  List<AuditEntry> findTop100ByOrderByOccurredAtDesc();
}
