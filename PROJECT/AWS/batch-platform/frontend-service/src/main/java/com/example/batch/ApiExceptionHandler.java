package com.example.batch;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

/** Exception Handler **/

@RestControllerAdvice
class ApiExceptionHandler {
  private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

  @ExceptionHandler(IllegalArgumentException.class)
  ResponseEntity<?> invalid(IllegalArgumentException e) {
    return ResponseEntity.badRequest().body(Map.of("error",
      e.getMessage() == null ? "Invalid input" : e.getMessage()));
  }

  @ExceptionHandler(ResponseStatusException.class)
  ResponseEntity<?> status(ResponseStatusException e) {
    return ResponseEntity.status(e.getStatusCode()).body(Map.of("error",
      e.getReason() == null ? "Request rejected" : e.getReason()));
  }

  @ExceptionHandler(org.springframework.dao.DataAccessException.class)
  ResponseEntity<?> database(org.springframework.dao.DataAccessException error) {
    log.error("database_error type={}", error.getClass().getSimpleName());
    return ResponseEntity.status(503).body(Map.of("error", "Database unavailable"));
  }

  @ExceptionHandler(RestClientException.class)
  ResponseEntity<?> unavailable(RestClientException error) {
    log.warn("backend_unavailable type={}", error.getClass().getSimpleName());
    return ResponseEntity.status(503).body(Map.of("error",
      "Service unavailable; keep the same requestKey when retrying"));
  }
}

