package com.example.batch;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Objects;
import java.util.Set;

/** Assign Tasks to Agent **/

@Component
class JobWorker {
  private static final Logger log = LoggerFactory.getLogger(JobWorker.class);
  private static final Set<String> FINAL_STATES = Set.of("SUCCESS", "FAILURE", "UNKNOWN");
  private final JobDao jobDao;
  private final AgentClient client;
  public final Map<String, String> agents;

  JobWorker(JobDao jobDao, AgentClient client,
            @Value("${batch.agent-app1-url}") String app1,
            @Value("${batch.agent-app2-url}") String app2) {
    this.jobDao = jobDao;
    this.client = client;
    this.agents = Map.of("app1", app1, "app2", app2);
  }

  @Scheduled(fixedDelay = 2000, initialDelay = 2000)
  void poll() {
    try {
      for(var job : jobDao.queued()) {
        String id = job.get("id").toString();
        if(jobDao.start(id)) {
          execute(job);
          break;
        }
      }
    } catch(Exception e) {
      log.error("worker_error type={}", e.getClass().getSimpleName());
    }
  }

  void execute(Map<String, Object> job) {
    String id = job.get("id").toString();
    String status = "UNKNOW";
    String detail = "Dispatch outcome unknown";

    try {
      String app = job.get("app").toString();
      var response = client.post(agents.get(app) + "/execute", Map.of(
        "id", id, "app", app,
        "type", job.get("job_type").toString(),
        "name", job.get("job_name").toString(),
        "businessDate", job.get("business_date").toString()), 90);
      String reported = Objects.toString(response.getHeaders().getFirst("X-Job-Status"), "UNKNOWN");

      if (response.getStatusCode().value() == 429 && "NOT_STARTED".equals(reported)) {
        if (jobDao.releaseUnclaimed(id)) {
          return;
        }
        detail = "Agent busy after execution was claimed; inspect outcome";
      } else {
        detail = response.getBody();
        if (response.getStatusCode().value() == 200 && FINAL_STATES.contains(reported)) {
          status = reported;
        }
      }

    } catch (Exception error) {
      detail = "Transport error: " + error.getClass().getSimpleName()
        + "; inspect business effects before rerun";
    }

    if (jobDao.finish(id, status, detail)) {
      log.info("job_id={} status={}", id, status);
    }
  }

  @Scheduled(fixedDelay = 30000, initialDelay = 30000)
  void recover() {
    try {
      int expired = jobDao.expireRunning();
      if (expired > 0) {
        log.warn("status=UNKNOWN recovered_unknown={}", expired);
      }
    } catch (Exception error) {
      log.error("recovery_error type={}", error.getClass().getSimpleName());
    }
  }

}
