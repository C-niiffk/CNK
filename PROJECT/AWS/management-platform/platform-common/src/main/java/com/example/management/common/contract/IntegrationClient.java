package com.example.management.common.contract;

public interface IntegrationClient {
  String request(String url, String method, String body) throws Exception;
}
