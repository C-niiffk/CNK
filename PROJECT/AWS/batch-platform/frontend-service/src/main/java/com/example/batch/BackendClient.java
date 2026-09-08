package com.example.batch;

import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.StreamUtils;
import org.springframework.web.client.RestClient;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;

public class BackendClient {
  private final HttpClient transport=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).followRedirects(HttpClient.Redirect.NEVER).build();
  private final String token=VerifyProcess.required("INTERNAL_TOKEN");
  ResponseEntity<String> post(String url, Map<String,String> fields, int seconds){
    var factory=new JdkClientHttpRequestFactory(transport);
    factory.setReadTimeout(Duration.ofSeconds(seconds));
    var form=new LinkedMultiValueMap<String,String>();fields.forEach(form::add);
    // exchange preserves non-2xx status, including explicit NOT_STARTED responses.
    return RestClient.builder().requestFactory(factory).build().post().uri(url)
      .header(HttpHeaders.AUTHORIZATION,"Bearer "+token)
      .contentType(MediaType.APPLICATION_FORM_URLENCODED).body(form)
      .exchange((request,response)->ResponseEntity.status(response.getStatusCode())
        .headers(response.getHeaders()).body(StreamUtils.copyToString(response.getBody(),java.nio.charset.StandardCharsets.UTF_8)));
  }
}
