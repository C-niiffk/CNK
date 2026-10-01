package com.example.management.plane.core;

import org.springframework.data.jpa.repository.JpaRepository;

public interface Documents extends JpaRepository<JsonDocument, String> {
}
