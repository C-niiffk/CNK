package com.example.batch;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/** Process Submission **/

@Service
public class JobService {
  private final JobDao jobDao;

  JobService(JobDao jobDao) {this.jobDao = jobDao;}

  String submit(Map<String, String> form) {
    VerifyProcess.validateJob(form);
    String key = VerifyProcess.field(form, "requestKey", "[A-Za-z0-9_-]{8,100}");
    String id = UUID.randomUUID().toString();

    try {
      jobDao.insert(id, form);
      return id;
    } catch (DuplicateKeyException duplicate) {
      var rows = jobDao.findByRequestKey(key);
      if(rows.isEmpty()) {
        throw duplicate;
      }
      var old = rows.getFirst();
      boolean same = Objects.equals(old.get("app"), form.get("app"))
        && Objects.equals(old.get("job_type"), form.get("type"))
        && Objects.equals(old.get("job_name"), form.get("name"))
        && Objects.equals(old.get("business_date"), form.get("businessDate"));
      if(!same) {
        throw new ResponseStatusException(HttpStatus.CONFLICT, "requestKey is already used for a different job");
      }
      return old.get("id").toString();
    }
  }

  boolean claim(Map<String, String> form) {
    VerifyProcess.validateJob(form);
    VerifyProcess.id(form);
    return jobDao.claim(form);
  }
}
