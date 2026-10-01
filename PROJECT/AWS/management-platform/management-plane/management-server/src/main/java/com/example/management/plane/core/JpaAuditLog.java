package com.example.management.plane.core;

import com.example.management.common.contract.AuditLog;
import com.example.management.common.contract.AuditRecord;
import java.util.List;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/** Infrastructure adapter shared by independently compiled management modules. */
@Component
public class JpaAuditLog implements AuditLog {
  private final Audits audits;

  public JpaAuditLog(Audits audits) {
    this.audits = audits;
  }

  @Override
  @Transactional
  public void record(String actor, String action, String target) {
    audits.save(new AuditEntry(actor, action, target));
  }

  @Override
  @Transactional(readOnly = true)
  public List<AuditRecord> recent() {
    return audits.findTop100ByOrderByOccurredAtDesc().stream()
      .map(a -> new AuditRecord(a.id, a.actor, a.action, a.target, a.occurredAt))
      .toList();
  }
}
