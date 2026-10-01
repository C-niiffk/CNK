package com.example.management.plane.core;

import jakarta.persistence.*;

@Entity
@Table(name = "platform_document")
public class JsonDocument {
  @Id
  public String id;
  @Lob
  @Column(name = "json_body", nullable = false)
  public String body;
  @Version
  @Column(name = "revision", nullable = false)
  public long revision;

  protected JsonDocument() {
  }

  public JsonDocument(String id, String body) {
    this.id = id;
    this.body = body;
  }
}
