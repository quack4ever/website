$(document).ready(function(){
    $("#title").hover(function(){
        $("#title").css("transform", "scale(1.1");
    }, function() {
        $("#title").css("transform", "scale(1.0");
    });

    $("#title").click(function(){
        window.location = "index.html";   
    });

    $("#logo").click(function(){
        window.location = "index.html";
    });
});