package com.example.batch;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.*;

/** Query Database **/

@Repository
class DBUtils {
  private final JdbcTemplate jdbc;

  DBUtils(JdbcTemplate jdbc) {this.jdbc = jdbc;}

  List<Map<String,Object>> query(String sql,Object...args){
    return jdbc.query(sql,(rs,row)->{
      Map<String,Object> out=new LinkedHashMap<>();
      for(int i=1;i<=rs.getMetaData().getColumnCount();i++){
        Object value=rs.getObject(i);
        out.put(rs.getMetaData().getColumnLabel(i).toLowerCase(Locale.ROOT),value==null?null:value.toString());
      }
      return out;
    },args);
  }
}
