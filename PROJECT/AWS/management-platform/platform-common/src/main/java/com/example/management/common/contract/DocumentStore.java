package com.example.management.common.contract;

import java.util.List;

public interface DocumentStore {
  List<String> list(String idPrefix);
  void put(String id, String body);
}
