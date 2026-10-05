package com.example.management.app.state;

import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.*;
import org.springframework.http.*;
import org.springframework.web.server.ResponseStatusException;

import java.sql.SQLException;

@RestController
@Profile("app2")
@RequestMapping({"/api/jobs", "/api/local-jobs"})
public class JobController {
  private final JobStore store;

  public JobController(JobStore store) {
    this.store = store;
  }

  public record Request(String requestId, String name, Integer totalSteps) {
  }

  @PostMapping
  public ResponseEntity<JobStore.Job> create(@RequestBody Request r) throws SQLException {
    try {
      var result = store.create(r.requestId(), r.name(), r.totalSteps() == null ? 60 : r.totalSteps());
      return ResponseEntity.status(result.created() ? 201 : 200).body(result.job());
    } catch (IllegalArgumentException e) {
      throw new ResponseStatusException(e.getMessage().startsWith("Invalid") ? HttpStatus.BAD_REQUEST : HttpStatus.CONFLICT, e.getMessage());
    }
  }

  @GetMapping("/{id}")
  public JobStore.Job get(@PathVariable String id) throws SQLException {
    return store.find(id).orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND));
  }

  @ExceptionHandler(SQLException.class)
  ResponseEntity<?> unavailable(SQLException e) {
    return ResponseEntity.status(503).body(java.util.Map.of("error", "Database unavailable; retry with the SAME requestId"));
  }
}
