package com.example.batch;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

@RestControllerAdvice
class ApiExceptionHandler {
  private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

  @ExceptionHandler(IllegalArgumentException.class)
  ResponseEntity<?> invalid(IllegalArgumentException error) {
    return ResponseEntity.badRequest().body(Map.of("error",
      error.getMessage() == null ? "Invalid input" : error.getMessage()));
  }

  @ExceptionHandler(ResponseStatusException.class)
  ResponseEntity<?> status(ResponseStatusException error) {
    return ResponseEntity.status(error.getStatusCode()).body(Map.of("error",
      error.getReason() == null ? "Request rejected" : error.getReason()));
  }

  @ExceptionHandler(RestClientException.class)
  ResponseEntity<?> unavailable(RestClientException error) {
    log.warn("backend_unavailable type={}", error.getClass().getSimpleName());
    return ResponseEntity.status(503).body(Map.of("error",
      "Service unavailable; keep the same requestKey when retrying"));
  }
}
