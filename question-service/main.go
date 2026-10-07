package main

import (
	"fmt"
	services "question-service/services/pdf"
)

func main() {
	f := "files/simulados/simulado2027-2edicao-prova-S2.pdf"

	r := services.ExtractPagesAsSVG(f)

	fmt.Printf("%v", r[2])

}
