package com.example.batch;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** DB Operation **/

@Repository
class JobDao {
  private final JdbcTemplate jdbc;

  JobDao(JdbcTemplate jdbc) { this.jdbc =  jdbc; }

  List<Map<String, Object>> findByRequestKey(String key) {
    return query("SELECT * FROM BATCH_OWNER.JOBS WHERE request_key=?", key);
  }

  List<Map<String, Object>> queued() {
    return query("""
      SELECT id,app,job_type,job_name,business_date
      FROM BATCH_OWNER.JOBS
      WHERE status='QUEUED'
      ORDER BY created_at,id FETCH FIRST 10 ROWS ONLY
      """);
  }

  void insert(String id, Map<String, String> form) {
    jdbc.update("""
      INSERT INTO BATCH_OWNER.JOBS
      (id,request_key,app,job_type,job_name,business_date,status)
      Values(?,?,?,?,?,?,'QUEUED')
      """, id, form.get("requestKey"), form.get("app"), form.get("type"),
      form.get("name"), form.get("businessDate"));
  }

  boolean start(String id) {
    return jdbc.update("""
      UPDATE BATCH_OWNER.JOBS SET status='RUNNING',started_at=?
      WHERE id=? AND status='QUEUED' AND execution_claimed=0
      """, now(), id) == 1;
  }

  boolean claim(Map<String, String> form) {
    // Agent 執行前再領取一次執行權，防止 HTTP 重送／代理重試造成重複執行。
    return jdbc.update("""
                UPDATE BATCH_OWNER.JOBS SET execution_claimed=1
                WHERE id=? AND status='RUNNING' AND execution_claimed=0
                  AND app=? AND job_type=? AND job_name=? AND business_date=?
                """, form.get("id"), form.get("app"), form.get("type"),
      form.get("name"), form.get("businessDate")) == 1;
  }

  boolean releaseUnclaimed(String id) {
    return jdbc.update("""
                UPDATE BATCH_OWNER.JOBS SET status='QUEUED',started_at=NULL
                WHERE id=? AND status='RUNNING' AND execution_claimed=0
                """, id) == 1;
  }

  boolean finish(String id, String status, String result) {
    return jdbc.update("""
                UPDATE BATCH_OWNER.JOBS SET status=?,result=?,finished_at=?
                WHERE id=? AND status='RUNNING'
                """, status, VerifyProcess.shortText(result), now(), id) == 1;
  }

  int expireRunning() {
    return jdbc.update("""
                UPDATE BATCH_OWNER.JOBS
                SET status='UNKNOWN',result='Worker lost or exceeded 5 minutes; inspect business effects',finished_at=?
                WHERE status='RUNNING' AND started_at < ?
                """, now(), Timestamp.from(Instant.now().minusSeconds(300)));
  }

  private Timestamp now() {return Timestamp.from(Instant.now());}

  private List<Map<String, Object>> query(String sql, Object... args) {
    return jdbc.query(sql, (rs, rowNumber) -> {
      Map<String, Object> row = new HashMap<>();
      var metadata = rs.getMetaData();
      for(int column =1; column <= metadata.getColumnCount(); column++) {
        Object value = rs.getObject(column);
        row.put(metadata.getColumnLabel(column).toLowerCase(Locale.ROOT),
          value ==null ? null : value.toString());
      }
      return row;
    }, args);
  }
}
