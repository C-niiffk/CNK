package com.example.batch;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.http.HttpHeaders;

import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/** Entry point from frontend to backend **/

@Component
class BackendClient {
  private final RestClient client;

  BackendClient(@Value("${batch.backend-url}") String baseUrl,
                @Value("${batch.internal-token}") String token) {
    if(baseUrl.isBlank() || token.length() < 32) {
      throw new IllegalArgumentException("Backend url and INTERNAL_TOKEN are required");
    }
    var transport = HttpClient.newBuilder()
      .connectTimeout(Duration.ofSeconds(5))
      .followRedirects(HttpClient.Redirect.NEVER)
      .build();
    var factory = new JdkClientHttpRequestFactory(transport);
    factory.setReadTimeout(Duration.ofSeconds(20));
    client = RestClient.builder()
      .baseUrl(baseUrl)
      .requestFactory(factory)
      .defaultHeader(HttpHeaders.AUTHORIZATION, "Bearer " + token)
      .build();
  }

  ResponseEntity<String> submit(Map<String, String> fields) {
    var form = new LinkedMultiValueMap<>();
    fields.forEach(form::add);

    return client.post().uri("/jobs")
      .contentType(MediaType.APPLICATION_FORM_URLENCODED)
      .body(form)
      .exchange((request, responses) ->
        ResponseEntity.status(responses.getStatusCode())
          .contentType(MediaType.APPLICATION_JSON)
          .body(new String(responses.getBody().readNBytes(8192), StandardCharsets.UTF_8)));
  }
}
