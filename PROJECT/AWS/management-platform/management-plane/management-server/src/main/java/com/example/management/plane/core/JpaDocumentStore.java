package com.example.management.plane.core;

import com.example.management.common.contract.DocumentStore;
import java.util.List;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/** Keeps the managed entity and its optimistic-lock revision inside the server. */
@Component
public class JpaDocumentStore implements DocumentStore {
  private final Documents documents;

  public JpaDocumentStore(Documents documents) {
    this.documents = documents;
  }

  @Override
  @Transactional(readOnly = true)
  public List<String> list(String idPrefix) {
    return documents.findAll().stream()
      .filter(d -> d.id.startsWith(idPrefix))
      .map(d -> d.body)
      .toList();
  }

  @Override
  @Transactional
  public void put(String id, String body) {
    var document = documents.findById(id).orElseGet(() -> new JsonDocument(id, body));
    document.body = body;
    documents.save(document);
  }
}
