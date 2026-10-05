package com.example.management.app.state;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import org.springframework.scheduling.annotation.Scheduled;
import org.slf4j.*;

import java.nio.file.*;
import java.util.UUID;

@Component
@Profile("app2")
public class JobWorker {
  private static final Logger log = LoggerFactory.getLogger(JobWorker.class);
  private final JobStore store;
  private final String owner = System.getenv().getOrDefault("SITE", "unknown") + ":" + UUID.randomUUID();
  private JobStore.Claim current;

  public JobWorker(JobStore store) {
    this.store = store;
  }

  @Scheduled(fixedDelayString = "${platform.jobs.step-ms:2000}")
  public void tick() {
    try {
      if (current == null) current = store.claim(owner).orElse(null);
      if (current == null) return;
      // Demonstration work: one committed checkpoint per tick. No external business side effects.
      if (!store.advance(current)) {
        current = null;
        return;
      }
      var job = store.find(current.id()).orElseThrow();
      log.info("job={} owner={} epoch={} checkpoint={}/{} status={}", job.id(), owner, job.epoch(), job.checkpoint(), job.totalSteps(), job.status());
      // Local state is disposable; it must NEVER decide ownership or the next checkpoint.
      String dir = System.getenv("APP2_DATA_DIR");
      if (dir != null) try {
        Files.writeString(Path.of(dir, job.id() + ".checkpoint"), job.toString());
      } catch (Exception e) {
        log.warn("Local checkpoint cache unavailable; committed DB progress is intact");
      }
      if (job.status().equals("SUCCEEDED")) current = null;
    } catch (Exception e) {
      current = null;
      log.warn("Job worker paused; DB ownership will be reacquired", e);
    }
  }
}
