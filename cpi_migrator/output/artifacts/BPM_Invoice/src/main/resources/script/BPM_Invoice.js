importClass(com.sap.gateway.ip.core.customdev.util.Message);
importClass(com.sap.it.api.ITApiFactory);
importClass(java.util.HashMap);

function processData(message) {

  var map = message.getHeaders();
  var queryPath = map.get("CamelHttpQuery");
  var doubleQuote = "%22";
  var singleQuote = "%27";
  var CurrentDateTime = map.get("CurrentDateTime");
  var validitydateFilter = "(UserValidityEndDate eq datetime'0001-01-01T00:00:00' or UserValidityEndDate ge datetime'" + CurrentDateTime + "')";
  var validitydateFilterForDeletedUsers = "UserValidityEndDate le datetime'" + CurrentDateTime + "'";

  var delta_quey_pattern_for_deleted_users = new RegExp('^filter=meta.lastModified%20gt%20%22[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z%22&count=[0-9]*&startIndex=[0-9]*&deleted=true$');
  var delta_query_pattern_with_top_skip = new RegExp('^filter=meta.lastModified%20gt%20%22[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z%22&count=[0-9]*&startIndex=[0-9]*$');
  var delta_query_pattern_with_top = new RegExp('^filter=meta.lastModified%20gt%20%22[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z%22&count=[0-9]*$');
  var delta_query_pattern_with_skip = new RegExp('^filter=meta.lastModified%20gt%20%22[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z%22&startIndex=[0-9]*$');
  var delta_query_pattern_without_paging = new RegExp('^filter=meta.lastModified%20gt%20%22[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z%22$');
  var countIndex_Pattern = new RegExp('count=[0-9]*');
  var startIndex_Pattern = new RegExp('startIndex=[0-9]*');
  var delta_timestamp_pattern = new RegExp('[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z');
  var country_filter_pattern_with_paging = new RegExp("^(?=.*\\bfilter=addresses\\.country%20eq%20%22[A-Z]{3}%22\\b)(?=.*\\b(startIndex|count)=\\d+\\b).*$");
  var country_filter_pattern = new RegExp('^filter=addresses.country%20eq%20%22[A-Z]*%22');
  var address_Pattern = new RegExp('addresses.country%20eq%20%22[A-Z]*%22');

  var queryParam = "";

  if (queryPath) {

    //filter=meta.lastModified gt "2023-02-06T00:00:00Z"&count=15&startIndex=5&deleted=true
    if (delta_quey_pattern_for_deleted_users.test(queryPath)) {

      var timeStamp = queryPath.match(delta_timestamp_pattern)[0];
      queryParam = '$filter=' + validitydateFilterForDeletedUsers + " and EntityLastChangedOn ge datetimeoffset'" + timeStamp + "'";

      var count = ((queryPath.match(countIndex_Pattern))[0]).substring(6);
      var startIndex = (queryPath.match(startIndex_Pattern))[0];
      var startCount = parseInt(startIndex.substring(11));

      if (startCount <= 1) startCount = 1;

      queryParam = queryParam + '&$top=' + count + '&$skip=' + (startCount - 1);

      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_startIndex", startIndex.substring(11));
      message.setProperty("p_itemsPerPage", count);
      message.setProperty("p_isQueryValid", "true");
      message.setHeader("DeltaTimeStamp", timeStamp);
      message.setHeader("GetDeletedUsers", "true");

      return message;

    }

    //filter=meta.lastModified gt "2022-01-01T00:00:00Z"&count=1000&startIndex=10
    if (delta_query_pattern_with_top_skip.test(queryPath)) {

      var timeStamp = queryPath.match(delta_timestamp_pattern)[0];
      queryParam = '$filter=(' + validitydateFilter + " and EntityLastChangedOn ge datetimeoffset'" + timeStamp + "')";

      var count = ((queryPath.match(countIndex_Pattern))[0]).substring(6);
      var startIndex = (queryPath.match(startIndex_Pattern))[0];
      var startCount = parseInt(startIndex.substring(11));

      if (startCount <= 1) startCount = 1;

      queryParam = queryParam + '&$top=' + count + '&$skip=' + (startCount - 1);

      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_startIndex", startIndex.substring(11));
      message.setProperty("p_itemsPerPage", count);
      message.setProperty("p_isQueryValid", "true");
      message.setHeader("DeltaTimeStamp", timeStamp);

      return message;

    }
    //filter=meta.lastModified gt "2023-01-01T00:00:00Z"&count=50
    if (delta_query_pattern_with_top.test(queryPath)) {

      var timeStamp = queryPath.match(delta_timestamp_pattern)[0];
      queryParam = '$filter=(' + validitydateFilter + " and EntityLastChangedOn ge datetimeoffset'" + timeStamp + "')";
      var count = ((queryPath.match(countIndex_Pattern))[0]).substring(6);

      queryParam = queryParam + '&$top=' + count;

      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_startIndex", "1");
      message.setProperty("p_itemsPerPage", count);
      message.setProperty("p_isQueryValid", "true");
      message.setHeader("DeltaTimeStamp", timeStamp);
      return message;

    }

    //filter=meta.lastModified gt "2023-01-01T00:00:00Z"&startIndex=50
    if (delta_query_pattern_with_skip.test(queryPath)) {

      var timeStamp = queryPath.match(delta_timestamp_pattern)[0];
      queryParam = '$filter=(' + validitydateFilter + " and EntityLastChangedOn ge datetimeoffset'" + timeStamp + "')";

      var startIndex = (queryPath.match(startIndex_Pattern))[0];
      var startCount = parseInt(startIndex.substring(11));

      if (startCount <= 1) startCount = 1;

      queryParam = queryParam + '&$top=100' + '&$skip=' + (startCount - 1);

      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_startIndex", startIndex.substring(11));
      message.setProperty("p_itemsPerPage", "100");
      message.setProperty("p_isQueryValid", "true");
      message.setHeader("DeltaTimeStamp", timeStamp);

      return message;

    }
    //filter=meta.lastModified gt "2023-01-01T00:00:00Z"
    if (delta_query_pattern_without_paging.test(queryPath)) {

      var timeStamp = queryPath.match(delta_timestamp_pattern)[0];
      queryParam = '$filter=(' + validitydateFilter + " and EntityLastChangedOn ge datetimeoffset'" + timeStamp + "')" + '&$top=100';

      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_startIndex", "1");
      message.setProperty("p_itemsPerPage", "100");
      message.setProperty("p_isQueryValid", "true");
      message.setHeader("DeltaTimeStamp", timeStamp);

      return message;

    }

    //Country filter
    if(country_filter_pattern_with_paging.test(queryPath) || country_filter_pattern.test(queryPath))
   {

      var countryFilterStartIndex = 1;
      var count = 0;
      var sourceCountry;
      
      if(address_Pattern.test(queryPath)) sourceCountry = ((queryPath.match(address_Pattern))[0]).substring(28,31);
      
      //value mapping call
      var valueMapService = ITApiFactory.getService(com.sap.it.api.mapping.ValueMappingApi, null);
      var targetCountryCode = valueMapService.getMappedValue("IPS", "Country", sourceCountry, "C4C", "CountryCode");
      
      if(targetCountryCode == null)
        targetCountryCode = sourceCountry;

      queryParam = '$filter=' + 'CountryCode eq %27' + targetCountryCode + '%27 and ' + validitydateFilter;

      if (countIndex_Pattern.test(queryPath)) count = ((queryPath.match(countIndex_Pattern))[0]).substring(6);

      if (startIndex_Pattern.test(queryPath)) {
        var startIndex = (queryPath.match(startIndex_Pattern))[0];
        countryFilterStartIndex = startIndex.substring(11);
      }


      if (count > 0) {
        queryParam = queryParam + '&$top=' + count + '&$skip=' + (countryFilterStartIndex - 1);
        message.setProperty("p_itemsPerPage", count);
      } else {
        queryParam = queryParam + '&$top=' + 100 + '&$skip=' + (countryFilterStartIndex - 1);
        message.setProperty("p_itemsPerPage", "100");
      }
      
  
      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_startIndex", countryFilterStartIndex + "");
      message.setProperty("p_isQueryValid", "true");
      message.setHeader("CountryCode", targetCountryCode);

      return message;

    }

    //$filter=(UserID%20eq%20%27P29092002%27%20or%20UserID%20eq%20%27P29092001%27) and (UserValidityEndDate eq datetime'0001-01-01T00:00:00' or UserValidityEndDate ge datetime'2020-09-29T09:03:24')
    if (queryPath.toLowerCase().indexOf('filter='.toLowerCase()) != -1) {

      //Since the attribute names are different in SCIM and C4C ODATA API, following attribute names need to be re-adjusted in the filter
      //Replace userName with UserID, email with Email and employeeNumber with EmployeeID
      var filterParam = queryPath.replace("userName", "UserID").replace("email", "Email").replace("employeeNumber", "EmployeeID");
      // Replace " with '
      filterParam = filterParam.replaceAll(doubleQuote, singleQuote);
      // Convert all UserID to Uppercase
      const regex = /%27[A-Za-z0-9]*%27/g;
      var userids = filterParam.match(regex);
      for (var i in userids) {
        filterParam = filterParam.replace(userids[i], userids[i].toUpperCase());
      }

      filterParam = filterParam.replace("filter=", "filter=(");
      filterParam = filterParam + ')' + ' and ' + validitydateFilter;
      message.setProperty("p_queryParam", '$' + filterParam);
      message.setProperty("p_isQueryValid", "true");
    }

    //$skip=4&$top=3&$filter=UserValidityEndDate eq datetime'0001-01-01T00:00:00' or UserValidityEndDate ge datetime'2020-09-16T17:51:55'
    else if ((queryPath.toLowerCase().indexOf('startIndex='.toLowerCase()) != -1) || (queryPath.toLowerCase().indexOf('count='.toLowerCase()) != -1)) {

      var pagingParam = queryPath.replace("startIndex", "skip").replace("count", "top");
      var tokenValues = pagingParam.split('&');
      for (var x in tokenValues) {
        if (tokenValues[x].startsWith("skip")) {
          var skipIndex = parseInt(tokenValues[x].substring(5));
          message.setProperty("p_startIndex", tokenValues[x].substring(5));
          if (skipIndex <= 1) skipIndex = 1;
          tokenValues[x] = "skip=" + (skipIndex - 1);
        }
        queryParam += ((queryParam === "") ? "": '&') + '$' + tokenValues[x];
        if (tokenValues[x].startsWith("top")) {
          message.setProperty("p_itemsPerPage", tokenValues[x].substring(4));
        }
      }
      queryParam = queryParam + '&$filter=' + validitydateFilter;
      message.setProperty("p_queryParam", queryParam);
      message.setProperty("p_isQueryValid", "true");

    } else {
      message.setProperty("p_isQueryValid", "false");
    }

  } else {
    queryPath = map.get("CamelHttpPath");
    var guid_pattern = new RegExp('[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}');
    if (guid_pattern.test(queryPath)) {
      var objectID = queryPath.replaceAll("-", "");
      message.setProperty("p_userguid", objectID.toUpperCase().trim());
      message.setProperty("p_odataread", "true");
      message.setProperty("p_isQueryValid", "true");
    } else if (queryPath === "/Users") {
      message.setProperty("p_queryParam", '$filter=' + validitydateFilter);
      message.setProperty("p_isQueryValid", "true");
    }

    else {
      message.setProperty("p_isQueryValid", "false");
    }

  }

  return message;
}