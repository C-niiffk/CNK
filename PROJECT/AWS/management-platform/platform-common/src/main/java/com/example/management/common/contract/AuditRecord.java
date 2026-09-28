package com.example.management.common.contract;

public record AuditRecord(String id, String actor, String action, String target, String occurredAt) {
}
