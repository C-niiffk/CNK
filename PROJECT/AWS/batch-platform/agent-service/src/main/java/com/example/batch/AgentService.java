package com.example.batch;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;

@Service
class AgentService {
  record Outcome(String status, String detail) { }
  private record Cached(Map<String, String> request, Outcome outcome) { }

  private static final Logger log = LoggerFactory.getLogger(AgentService.class);
  private final ReentrantLock execution = new ReentrantLock();
  private final Map<String, Cached> completed = new LinkedHashMap<>();
  private final InternalClient client;
  private final String appId;
  private final String claimUrl;
  private final String applicationUrl;
  private final Path scriptDirectory;
  private final Path workDirectory;
  private final int timeout;

  AgentService(InternalClient client,
               @Value("${batch.app-id}") String appId,
               @Value("${batch.claim-url}") String claimUrl,
               @Value("${batch.application-url}") String applicationUrl,
               @Value("${batch.script-directory}") String scriptDirectory,
               @Value("${batch.work-directory}") String workDirectory,
               @Value("${batch.timeout-seconds}") int timeout) throws IOException {
    if (!Set.of("app1", "app2").contains(appId) || timeout < 1 || timeout > 60) {
      throw new IllegalArgumentException("APP_ID must be app1/app2; timeout must be 1..60 seconds");
    }
    URI uri = URI.create(applicationUrl);
    if (!"http".equals(uri.getScheme()) || !"127.0.0.1".equals(uri.getHost())) {
      throw new IllegalArgumentException("APPLICATION_URL must use http://127.0.0.1:<port>");
    }
    this.client = client;
    this.appId = appId;
    this.claimUrl = claimUrl;
    this.applicationUrl = applicationUrl;
    this.scriptDirectory = Path.of(scriptDirectory).toAbsolutePath().normalize();
    this.workDirectory = Path.of(workDirectory).toAbsolutePath().normalize();
    this.timeout = timeout;
    Files.createDirectories(this.workDirectory);
  }

  ResponseEntity<?> execute(Map<String, String> request) {
    VerifyProcess.validateJob(request);
    String id = VerifyProcess.id(request);
    if (!appId.equals(request.get("app"))) {
      throw new IllegalArgumentException("Wrong application");
    }
    if (!execution.tryLock()) {
      return ResponseEntity.status(429).header("X-Job-Status", "NOT_STARTED")
        .body(Map.of("error", "Agent busy"));
    }

    try {
      Cached cached = completed.get(id);
      if (cached != null) {
        if (!cached.request().equals(request)) {
          return ResponseEntity.status(409).body(Map.of("error", "id reused with different payload"));
        }
        return response(id, cached.outcome());
      }

      Outcome outcome;
      try {
        var claim = client.post(claimUrl, request, 15);
        if (claim.getStatusCode().value() != 201) {
          outcome = new Outcome("UNKNOWN", "Execution claim denied; no execution on this delivery");
        } else if ("SH".equals(request.get("type"))) {
          outcome = shell(id, request.get("businessDate"));
        } else {
          outcome = api(id, request.get("businessDate"));
        }
      } catch (Exception error) {
        if (error instanceof InterruptedException) {
          Thread.currentThread().interrupt();
        }
        outcome = new Outcome("UNKNOWN", error.getClass().getSimpleName()
          + "; inspect business effects before rerun");
      }

      if (completed.size() >= 1000) {
        completed.remove(completed.keySet().iterator().next());
      }
      completed.put(id, new Cached(Map.copyOf(request), outcome));
      log.info("job_id={} status={}", id, outcome.status());
      return response(id, outcome);
    } finally {
      execution.unlock();
    }
  }

  private ResponseEntity<?> response(String id, Outcome outcome) {
    return ResponseEntity.ok().header("X-Job-Status", outcome.status())
      .body(Map.of("id", id, "status", outcome.status(), "detail", outcome.detail()));
  }

  Outcome api(String id, String businessDate) {
    var response = client.post(applicationUrl + "/batch/reconcile",
      Map.of("id", id, "businessDate", businessDate), timeout);
    String reported = Objects.toString(response.getHeaders().getFirst("X-Job-Status"), "UNKNOWN");
    boolean valid = response.getStatusCode().value() == 200
      && Set.of("SUCCEEDED", "FAILED").contains(reported);
    return new Outcome(valid ? reported : "UNKNOWN", VerifyProcess.shortText(response.getBody()));
  }

  Outcome shell(String id, String businessDate) throws IOException, InterruptedException {
    Path script = scriptDirectory.resolve("reconcile.sh").toRealPath();
    ProcessBuilder builder = new ProcessBuilder("/bin/sh", script.toString(), businessDate, id);
    builder.directory(workDirectory.toFile()).redirectErrorStream(true);
    builder.environment().clear();
    builder.environment().put("PATH", "/usr/bin:/bin");
    builder.environment().put("LANG", "C.UTF-8");
    builder.environment().put("BATCH_DATA_FILE", scriptDirectory.resolve("sample-data.csv").toString());
    builder.environment().put("BATCH_WORK_DIR", workDirectory.toString());

    Process process = builder.start();
    ByteArrayOutputStream output = new ByteArrayOutputStream();
    Thread reader = Thread.ofVirtual().name("script-output").start(() -> {
      try (var input = process.getInputStream()) {
        byte[] buffer = new byte[4096];
        int length;
        while ((length = input.read(buffer)) != -1) {
          int keep = Math.min(length, Math.max(0, 8192 - output.size()));
          output.write(buffer, 0, keep);
        }
      } catch (IOException ignored) {
      }
    });

    boolean exited;
    try {
      exited = process.waitFor(timeout, TimeUnit.SECONDS);
      if (!exited) {
        stop(process);
      }
      reader.join(2000);
    } catch (InterruptedException error) {
      stop(process);
      throw error;
    }
    if (reader.isAlive()) {
      process.getInputStream().close();
      reader.join(1000);
      return new Outcome("UNKNOWN", "Script left open output; run child processes in foreground");
    }
    if (!exited) {
      return new Outcome("UNKNOWN", "SH timeout; business effects may already exist");
    }
    return new Outcome(process.exitValue() == 0 ? "SUCCEEDED" : "FAILED",
      VerifyProcess.shortText(output.toString(StandardCharsets.UTF_8)));
  }

  private void stop(Process process) {
    process.descendants().forEach(ProcessHandle::destroyForcibly);
    process.destroyForcibly();
  }
}
