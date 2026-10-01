package com.example.management.plane.core;

import java.util.Map;

import org.springframework.web.bind.annotation.*;
import org.springframework.http.*;
import com.alibaba.nacos.api.exception.NacosException;

@RestControllerAdvice
public class Errors {
  @ExceptionHandler(IllegalArgumentException.class)
  ResponseEntity<?> invalid(IllegalArgumentException e) {
    return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
  }

  @ExceptionHandler({NacosException.class, java.io.IOException.class, IllegalStateException.class})
  ResponseEntity<?> upstream(Exception e) {
    return ResponseEntity.status(502).body(Map.of("error", "Dependency unavailable; inspect server logs"));
  }
}
