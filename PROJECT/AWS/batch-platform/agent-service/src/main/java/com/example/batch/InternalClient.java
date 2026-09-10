package com.example.batch;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/** Call Backend or Application **/

@Component
class InternalClient {
  private final String token;
  private final HttpClient transport = HttpClient.newBuilder()
    .connectTimeout(Duration.ofSeconds(5))
    .followRedirects(HttpClient.Redirect.NEVER)
    .build();

  InternalClient(@Value("${batch.internal-token}") String token) {this.token = token;}

  ResponseEntity<String> post(String url, Map<String, String> values, int timeoutSeconds) {
    var factory = new JdkClientHttpRequestFactory(transport);
    factory.setReadTimeout(Duration.ofSeconds(timeoutSeconds));
    var fields = new LinkedMultiValueMap<>();
    values.forEach(fields::add);
    return RestClient.builder().requestFactory(factory).build()
      .post().uri(url)
      .header(HttpHeaders.AUTHORIZATION, "Bearer "+token)
      .contentType(MediaType.APPLICATION_FORM_URLENCODED)
      .body(fields)
      .exchange((request, response) -> ResponseEntity.status(response.getStatusCode())
        .headers(response.getHeaders())
        .body(new String(response.getBody().readNBytes(8192), StandardCharsets.UTF_8)));
  }
}
