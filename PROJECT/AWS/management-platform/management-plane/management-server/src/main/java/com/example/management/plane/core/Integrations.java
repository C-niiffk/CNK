package com.example.management.plane.core;

import com.example.management.common.contract.IntegrationClient;

import java.net.*;
import java.net.http.*;
import java.time.Duration;

import com.example.management.common.contract.IntegrationClient;
import org.springframework.stereotype.Component;

@Component
public class Integrations implements IntegrationClient {
  private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();

  @Override
  public String request(String url, String method, String body) throws Exception {
    var b = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(8)).header("Content-Type", "application/json");
    b.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
    var response = client.send(b.build(), HttpResponse.BodyHandlers.ofString());
    if (response.statusCode() >= 400) throw new IllegalStateException("Upstream returned " + response.statusCode());
    return response.body();
  }
}
