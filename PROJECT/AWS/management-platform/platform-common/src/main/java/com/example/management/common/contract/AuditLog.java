package com.example.management.common.contract;

import java.util.List;

public interface AuditLog {
  void record(String actor, String action, String target);
  List<AuditRecord> recent();
}
